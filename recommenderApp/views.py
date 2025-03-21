from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from .models import (StudentProfile, Prediction, Feedback, Testimonial, IQQuestion, IQTestResult,
                     ContactMessageLanding, ContactMessage, TeacherProfile, RecommendationOverride, School,
                     OTPVerification, PasswordReset)
import joblib
import numpy as np
import random
import pandas as pd
import os
import re
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, F
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import json
from .utils import send_otp_email, send_reset_otp_email

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) #BASE_DIR

#path to the saved models folder
MODEL_PATH = os.path.join(BASE_DIR, "recommenderApp", "saved_models")

#Try/except block for model loading to handle errors
try:
    rf_classifier = joblib.load(os.path.join(MODEL_PATH, "random_forest_model.pkl"))
    knn_model = joblib.load(os.path.join(MODEL_PATH, "knn_recommender_model.pkl"))
    similarity_matrix = joblib.load(os.path.join(MODEL_PATH, "similarity_matrix.pkl"))
    label_mappings = joblib.load(os.path.join(MODEL_PATH, "label_mappings.pkl"))
    csv_file_path = os.path.join(MODEL_PATH, 'rwanda_students_final_v3.csv')
    
    df = pd.read_csv(csv_file_path)
    
    #define categorical columns and target variable
    categorical_cols = ["gender", "school_type", "location", "parental_education_level",
                        "internet_access", "parental_career", "extracurricular_activity", 
                        'interest', 'recommended_stream']
    features = df.drop(columns=["student_id", "recommended_stream"])
    target = df["recommended_stream"]
    
    MODELS_LOADED = True
except Exception as e:
    print(f"Error loading models: {e}")
    MODELS_LOADED = False

def landing(request):
    """Landing page view"""
    return render(request, 'landing.html')

def submit_contact_landing(request):
    """Handle contact form submission"""
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            email = request.POST.get('email')
            message = request.POST.get('message')
            
            #save to database using correct model name
            ContactMessageLanding.objects.create(
                user=name,
                email=email,
                message=message
            )
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

# Add these new views to views.py

@require_POST
@csrf_exempt
def reset_password_otp(request):
    """API endpoint to send OTP for password reset"""
    try:
        data = json.loads(request.body)
        email = data.get('email')
        
        if not email:
            return JsonResponse({'success': False, 'error': 'Email is required'})
        
        # Check if email exists
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Don't reveal that the email doesn't exist for security
            return JsonResponse({'success': True})  # Pretend success
        
        # Generate OTP
        otp_obj = OTPVerification.generate_otp(user)
        
        # Send OTP email
        send_reset_otp_email(email, otp_obj.otp)
        
        # Store user ID in session
        request.session['reset_user_id'] = user.id
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

def forgot_password(request):
    """Handle forgot password requests"""
    if request.method == 'POST':
        email = request.POST.get('email')
        otp = request.POST.get('otp')
        
        # Find user by email
        try:
            user = User.objects.get(email=email)
            
            # Verify OTP
            try:
                otp_obj = OTPVerification.objects.filter(user=user).latest('created_at')
                
                if not otp_obj.is_valid():
                    return render(request, 'forgot_password.html', {
                        'error_message': 'OTP has expired. Please request a new one.'
                    })
                
                if otp_obj.otp != otp:
                    return render(request, 'forgot_password.html', {
                        'error_message': 'Invalid OTP. Please try again.'
                    })
                
                # OTP is valid - generate password reset token
                token_obj = PasswordReset.generate_token(user)
                
                # Redirect to reset password page
                return redirect('reset_password', token=token_obj.token)
                
            except OTPVerification.DoesNotExist:
                return render(request, 'forgot_password.html', {
                    'error_message': 'Please click "Get OTP" to receive a verification code.'
                })
                
        except User.DoesNotExist:
            return render(request, 'forgot_password.html', {
                'error_message': 'Email not found. Please check and try again.'
            })
    
    return render(request, 'forgot_password.html')

def reset_password(request, token):
    """Handle password reset with token"""
    # Verify token
    try:
        token_obj = PasswordReset.objects.get(token=token)
        
        if not token_obj.is_valid():
            return render(request, 'reset_password.html', {
                'error_message': 'This password reset link has expired or been used.'
            })
            
        if request.method == 'POST':
            password = request.POST.get('password')
            confirm_password = request.POST.get('confirm_password')
            
            # Validate passwords
            if password != confirm_password:
                return render(request, 'reset_password.html', {
                    'error_message': 'Passwords do not match.',
                    'token': token
                })
            
            # Server-side password validation
            if len(password) < 8 or not re.search(r'[A-Z]', password) or not re.search(r'[a-z]', password) or not re.search(r'[0-9]', password) or not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
                return render(request, 'reset_password.html', {
                    'error_message': 'Password must contain at least 8 characters, including uppercase, lowercase, number, and special character.',
                    'token': token
                })
            
            # Update password
            user = token_obj.user
            user.set_password(password)
            user.save()
            
            # Mark token as used
            token_obj.is_used = True
            token_obj.save()
            
            # Show success message
            messages.success(request, 'Your password has been reset successfully! You can now login with your new password.')
            
            # Redirect based on user type
            if hasattr(user, 'student_profile'):
                return redirect('student_login')
            elif hasattr(user, 'teacher_profile'):
                return redirect('teacher_login')
            else:
                return redirect('student_login')  # Default fallback
                
        return render(request, 'reset_password.html', {'token': token})
        
    except PasswordReset.DoesNotExist:
        return render(request, 'reset_password.html', {
            'error_message': 'Invalid password reset link.'
        })

@require_POST
@csrf_exempt  # For simplicity in this example, but consider using proper CSRF protection
def send_otp(request):
    """API endpoint to send OTP to email"""
    try:
        data = json.loads(request.body)
        email = data.get('email')
        
        if not email:
            return JsonResponse({'success': False, 'error': 'Email is required'})
        
        # Check if email already exists in a verified account
        if User.objects.filter(email=email, student_profile__is_verified=True).exists() or \
           User.objects.filter(email=email, teacher_profile__is_verified=True).exists():
            return JsonResponse({'success': False, 'error': 'Email already registered'})
        
        # Create a temporary user if it doesn't exist
        user, created = User.objects.get_or_create(
            username=email,
            defaults={'email': email, 'is_active': False}  # Inactive until verified
        )
        
        # Generate OTP
        otp_obj = OTPVerification.generate_otp(user)
        
        # Send OTP email
        send_otp_email(email, otp_obj.otp)
        
        # Store user ID in session
        request.session['temp_user_id'] = user.id
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

def student_signup(request):
    """Handle student registration with inline OTP verification"""
    schools = School.objects.all().order_by('name')
    
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        school_id = request.POST.get('school')
        email = request.POST.get('email')
        otp = request.POST.get('otp')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        
        # Validate form data
        if password != confirm_password:
            return render(request, 'student_signup.html', {'error_message': 'Passwords do not match', 'schools': schools})
        if len(password) < 8 or not re.search(r'[A-Z]', password) or not re.search(r'[a-z]', password) or not re.search(r'[0-9]', password) or not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
            return render(request, 'student_signup.html', {'error_message': 'Password must contain at least 8 characters, including uppercase, lowercase, number, and special character.','schools': schools})
        # Find user by email
        try:
            user = User.objects.get(username=email)
            
            # Verify OTP
            try:
                otp_obj = OTPVerification.objects.filter(user=user).latest('created_at')
                
                if not otp_obj.is_valid():
                    return render(request, 'student_signup.html', {
                        'error_message': 'OTP has expired. Please request a new one.',
                        'schools': schools
                    })
                
                if otp_obj.otp != otp:
                    return render(request, 'student_signup.html', {
                        'error_message': 'Invalid OTP. Please try again.',
                        'schools': schools
                    })
                
                # OTP is valid - proceed with account creation
                # Get school object
                try:
                    school = School.objects.get(id=school_id)
                except School.DoesNotExist:
                    return render(request, 'student_signup.html', {
                        'error_message': 'Invalid school selected',
                        'schools': schools
                    })
                
                # Update user account
                user.set_password(password)
                user.is_active = True
                user.save()
                
                # Create student profile (verified)
                student = StudentProfile.objects.create(
                    user=user,
                    full_name=full_name,
                    email=email,
                    school=school,
                    is_verified=True
                )
                
                # Log the user in
                login(request, authenticate(username=email, password=password))
                request.session['student_id'] = student.id
                
                messages.success(request, 'Account created successfully!')
                return redirect('home')
                
            except OTPVerification.DoesNotExist:
                return render(request, 'student_signup.html', {
                    'error_message': 'Please click "Get OTP" to receive a verification code.',
                    'schools': schools
                })
                
        except User.DoesNotExist:
            return render(request, 'student_signup.html', {
                'error_message': 'Please click "Get OTP" to receive a verification code.',
                'schools': schools
            })
    
    return render(request, 'register_student.html', {'schools': schools})

def student_login(request):
    """Handle student login"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        #find user by email
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return render(request, 'student_login.html', {'error_message': 'Invalid email or password'})
        
        user = authenticate(request, username=user.username, password=password) #authenticate user
        
        if user is not None:
            #check if user has a student profile
            try:
                student_profile = StudentProfile.objects.get(user=user)
                login(request, user)
                request.session['student_id'] = student_profile.id
                return redirect('home')
            except StudentProfile.DoesNotExist:
                return render(request, 'student_login.html', {'error_message': 'This account is not registered as a student'})
        else:
            return render(request, 'student_login.html', {'error_message': 'Invalid email or password'})
    
    return render(request, 'student_login.html')

def teacher_signup(request):
    """Handle teacher registration with inline OTP verification"""
    schools = School.objects.all().order_by('name')
    
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        school_id = request.POST.get('school')
        email = request.POST.get('email')
        subject_specialization = request.POST.get('subject_specialization')
        otp = request.POST.get('otp')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        
        # Validate form data
        if password != confirm_password:
            return render(request, 'teacher_signup.html', {'error_message': 'Passwords do not match', 'schools': schools})
        
        # Find user by email
        try:
            user = User.objects.get(username=email)
            
            # Verify OTP
            try:
                otp_obj = OTPVerification.objects.filter(user=user).latest('created_at')
                
                if not otp_obj.is_valid():
                    return render(request, 'teacher_signup.html', {
                        'error_message': 'OTP has expired. Please request a new one.',
                        'schools': schools
                    })
                
                if otp_obj.otp != otp:
                    return render(request, 'teacher_signup.html', {
                        'error_message': 'Invalid OTP. Please try again.',
                        'schools': schools
                    })
                
                # OTP is valid - proceed with account creation
                # Get school object
                try:
                    school = School.objects.get(id=school_id)
                except School.DoesNotExist:
                    return render(request, 'teacher_signup.html', {
                        'error_message': 'Invalid school selected',
                        'schools': schools
                    })
                
                # Update user account
                user.set_password(password)
                user.is_active = True
                user.save()
                
                # Create teacher profile (verified)
                teacher = TeacherProfile.objects.create(
                    user=user,
                    full_name=full_name,
                    email=email,
                    school=school,
                    subject_specialization=subject_specialization,
                    is_verified=True
                )
                
                # Log the user in
                login(request, authenticate(username=email, password=password))
                request.session['teacher_email'] = teacher.email
                
                messages.success(request, 'Account created successfully!')
                return redirect('teacher_dashboard')
                
            except OTPVerification.DoesNotExist:
                return render(request, 'teacher_signup.html', {
                    'error_message': 'Please click "Get OTP" to receive a verification code.',
                    'schools': schools
                })
                
        except User.DoesNotExist:
            return render(request, 'teacher_signup.html', {
                'error_message': 'Please click "Get OTP" to receive a verification code.',
                'schools': schools
            })
    
    return render(request, 'register_teacher.html', {'schools': schools})


def teacher_login(request):
    """Handle teacher login"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        #find user by email
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return render(request, 'teacher_login.html', {'error_message': 'Invalid email or password'})
        
        user = authenticate(request, username=user.username, password=password) #authenticate user
        
        if user is not None:
            #check if user has a teacher profile
            try:
                teacher_profile = TeacherProfile.objects.get(user=user)
                login(request, user)
                request.session['teacher_email'] = teacher_profile.email  # Keep existing session mechanism
                return redirect('teacher_dashboard')
            except TeacherProfile.DoesNotExist:
                return render(request, 'teacher_login.html', {'error_message': 'This account is not registered as a teacher'})
        else:
            return render(request, 'teacher_login.html', {'error_message': 'Invalid email or password'})
    
    return render(request, 'teacher_login.html')

# Legacy signin functions (for backward compatibility)
def student_signin(request):
    """Handle legacy student signin (redirects to signup)"""
    return redirect('student_signup')

def teacher_signin(request):
    """Handle legacy teacher signin (redirects to signup)"""
    return redirect('teacher_signup')

def logout_view(request):
    """Handle user logout"""
    logout(request)
    #clear session
    if 'student_id' in request.session:
        del request.session['student_id']
    if 'teacher_email' in request.session:
        del request.session['teacher_email']
    return redirect('landing')

# Homepage view function
@login_required(login_url='student_login')
def home(request):
    #check if user is logged in
    student_id = request.session.get('student_id')
    if not student_id:
        return redirect('landing')
    
    try:
        user = StudentProfile.objects.get(id=student_id)
    except StudentProfile.DoesNotExist:
        # Invalid session, redirect to landing
        del request.session['student_id']
        return redirect('landing')
    
    testimonials = Testimonial.objects.order_by('-created_at')[:6] #display testimonials
    
    #check if user has a prediction and testimonial
    user_has_prediction = Prediction.objects.filter(student=user).exists()
    user_has_testimonial = Testimonial.objects.filter(student=user).exists()
    
    context = {
        'user': user,
        'testimonials': testimonials,
        'user_has_prediction': user_has_prediction,
        'user_has_testimonial': user_has_testimonial
    }
    
    return render(request, 'home.html', context)


# hybrid Recommendation Function
def hybrid_recommend(student_input):
    student_df = pd.DataFrame([student_input])
    #ensure all feature columns are present
    missing_cols = [col for col in features.columns if col not in student_df.columns]
    for col in missing_cols:
        student_df[col] = 0
    student_df = student_df[features.columns] #to match training order

    predicted_stream = rf_classifier.predict(student_df)[0]    #predict stream using Random Forest (content-based filtering)

    distances, similar_student_indices = knn_model.kneighbors(student_df, n_neighbors=5)     #find the closest matches using k-NN (collaborative filtering)
    collaborative_recs = target.iloc[similar_student_indices[0][1:]].tolist()  #ignore first one (itself)

    final_recommendations = list(dict.fromkeys([predicted_stream] + collaborative_recs)) #remove duplicates while keeping order
    all_possible_streams = target.unique().tolist()  #at least 3 recommendations
    
    all_possible_streams = [s for s in all_possible_streams if isinstance(s, (int, float, np.integer, np.floating))]    #remove non-numeric values

    additional_recs = [s for s in all_possible_streams if s not in final_recommendations]
    random.shuffle(additional_recs)
    
    while len(final_recommendations) < 3 and additional_recs:  #adding a subject untill its up to 3 unique recommendations
        final_recommendations.append(additional_recs.pop())

    return predicted_stream, final_recommendations

#predict_student function
@login_required(login_url='student_login')
def predict_student(request):
    context = {}
    if not MODELS_LOADED:
        context["error_message"] = "System is currently unavailable. Please try again later." #confirms if models are loaded
        return render(request, "predict.html", context)

    #step 1: Initial form submission from home page
    if request.method == "POST" and "name" in request.POST and "school" in request.POST and "age" not in request.POST:
        name = request.POST.get("name", "").strip()
        school = request.POST.get("school", "").strip()

        if not name or not school:
            return redirect("home")

        context["name"] = name
        context["school"] = school

        request.session["student_name"] = name
        request.session["school_name"] = school

        return render(request, "predict.html", context)

    #step 2: Detailed student form submission
    elif request.method == "POST" and "age" in request.POST:
        name = request.session.get("student_name", request.POST.get("name", "Student"))
        school = request.session.get("school_name", request.POST.get("school", ""))
        context["name"] = name
        context["school"] = school

        required_fields = [
            "age", "math_score", "english_score", "science_score", 
            "history_score", "attendance_rate", "study_hours_per_week", 
            "household_income", "gender", "school_type", "location", 
            "parental_education_level", "internet_access", "parental_career", 
            "extracurricular_activity", "interest"
        ]

        #check for missing fields
        missing_fields = [field for field in required_fields if field not in request.POST or not request.POST[field]]
        if missing_fields:
            context["error_message"] = f"Please fill out all required fields. Missing: {', '.join(missing_fields)}"
            return render(request, "predict.html", context)

        try:
            student_input = {}

            #validate numerical fields
            numerical_fields = [
                "age", "math_score", "english_score", "science_score", 
                "history_score", "attendance_rate", "study_hours_per_week", 
                "household_income"
            ]

            for field in numerical_fields:
                value = int(request.POST[field])
                
                if field in ["math_score", "english_score", "science_score", "history_score"] and not (0 <= value <= 100):
                    raise ValueError(f"{field.replace('_', ' ').title()} must be between 0 and 100")
                elif field == "attendance_rate" and not (0 <= value <= 100):
                    raise ValueError("Attendance rate must be between 0 and 100")
                elif field == "age" and not (10 <= value <= 25):
                    raise ValueError("Age must be between 10 and 25")

                student_input[field] = value

            #categorical fields
            categorical_fields = [
                "gender", "school_type", "location", "parental_education_level", 
                "internet_access", "parental_career", "extracurricular_activity", "interest"
            ]

            for field in categorical_fields:
                student_input[field] = int(request.POST[field])

            predicted_code, recommendations = hybrid_recommend(student_input) #predict subject and recommendations
            
            #label mappings
            label_mappings = {
                0: "Arts",
                1: "Business",
                2: "Healthcare",
                3: "Humanities",
                4: "STEM"
            }

            #prediction is properly mapped from integer to string
            predicted_subject = label_mappings.get(predicted_code, f"Unknown ({predicted_code})")
            
            #recommendations are properly mapped and unique
            decoded_recommendations = []
            for rec in recommendations:
                #convert any numpy type to int before checking the mapping
                rec_code = int(rec) if isinstance(rec, (np.int32, np.int64)) else rec
                
                if isinstance(rec_code, int):
                    decoded_recommendations.append(label_mappings.get(rec_code, f"Unknown ({rec_code})"))
                else:
                    decoded_recommendations.append(rec)

            decoded_recommendations = list(dict.fromkeys(decoded_recommendations)) #remove duplicates while maintaining order
            
            #remove the predicted subject from recommendations if it exists
            if predicted_subject in decoded_recommendations:
                decoded_recommendations.remove(predicted_subject)
            
            #to confirm 3 recommendations
            all_subjects = [label_mappings[i] for i in range(5)]
            available_subjects = [subject for subject in all_subjects if subject != predicted_subject and subject not in decoded_recommendations]
            
            #if not up to 3, add the next in line
            while len(decoded_recommendations) < 3 and available_subjects:
                next_subject = available_subjects.pop(0)
                decoded_recommendations.append(next_subject)
            
            recommended_subjects = decoded_recommendations[:3] #top 3

            student_id = request.session.get('student_id') #check if user is logged in
            if student_id:
                try:
                    student = StudentProfile.objects.get(id=student_id)
                    
                    #create and save prediction with the integer code
                    #store the original predicted code for database consistency
                    prediction = Prediction.objects.create(
                        student=student,
                        predicted_subject=predicted_code,
                        recommended_subjects=",".join(recommended_subjects)
                    )

                    if not student.age:  #only update if fields are empty
                        student.age = student_input["age"]
                        student.math_score = student_input["math_score"]
                        student.english_score = student_input["english_score"]
                        student.science_score = student_input["science_score"]
                        student.history_score = student_input["history_score"]
                        student.attendance_rate = student_input["attendance_rate"]
                        student.study_hours_per_week = student_input["study_hours_per_week"]
                        student.household_income = student_input["household_income"]
                        student.gender = student_input["gender"]
                        student.school_type = student_input["school_type"]
                        student.location = student_input["location"]
                        student.parental_education_level = student_input["parental_education_level"]
                        student.internet_access = student_input["internet_access"]
                        student.parental_career = student_input["parental_career"]
                        student.extracurricular_activity = student_input["extracurricular_activity"]
                        student.interest = student_input["interest"]
                        student.save()
                    
                    return redirect('result', prediction_id=prediction.id) #redirect to results page with prediction ID
                    
                except StudentProfile.DoesNotExist:
                    #to store prediction data in session for non-logged in users or failed lookup
                    request.session['temp_prediction'] = {
                        'predicted_subject': predicted_subject,  # Store the string representation
                        'recommended_subjects': recommended_subjects,
                        'student_input': student_input
                    }
                    return redirect('result')
            else:
                #store prediction data in session for non-logged in users
                request.session['temp_prediction'] = {
                    'predicted_subject': predicted_subject,  # Store the string representation
                    'recommended_subjects': recommended_subjects,
                    'student_input': student_input
                }
                return redirect('result')

        except ValueError as e:
            context["error_message"] = str(e)
        except KeyError as e:
            context["error_message"] = f"Missing field: {e}. Please fill out all fields."
        except Exception as e:
            import traceback
            traceback.print_exc()
            context["error_message"] = f"An error occurred: {str(e)}"

    #step 3: Handle GET request
    else:
        context["name"] = request.session.get("student_name", "")
        context["school"] = request.session.get("school_name", "")

    return render(request, "predict.html", context)


def result_view(request, prediction_id=None):
    context = {}
    
    #label mappings for converting integer codes to string representations
    label_mappings = {
        0: "Arts",
        1: "Business",
        2: "Healthcare",
        3: "Humanities",
        4: "STEM"
    }
    
    #check if user is logged in
    student_id = request.session.get('student_id')
    if student_id:
        try:
            user = StudentProfile.objects.get(id=student_id)
            context['user'] = user
            
            if prediction_id: #check if user have a prediction ID
                try:
                    prediction = Prediction.objects.get(id=prediction_id, student=user)
                    context['prediction'] = prediction
                    context['prediction_found'] = True
                    
                    #map the integer predicted_subject to its string representation
                    predicted_subject = label_mappings.get(prediction.predicted_subject, 
                                                        f"Unknown ({prediction.predicted_subject})")
                    context['predicted_subject'] = predicted_subject
                    
                    #then convert comma-separated string back to list
                    recommended_subjects = prediction.recommended_subjects.split(',')
                    context['recommended_subjects'] = recommended_subjects
                    
                    #confirm if user has already submitted a testimonial for this prediction
                    user_testimonial = Testimonial.objects.filter(student=user, prediction=prediction).first()
                    context['has_testimonial'] = user_testimonial is not None
                    context['user_testimonial'] = user_testimonial
                    
                except Prediction.DoesNotExist:
                    context['error_message'] = "The requested prediction was not found or doesn't belong to you."
            else:
                # Get latest prediction for this user if no specific ID
                prediction = Prediction.objects.filter(student=user).order_by('-created_at').first()
                if prediction:
                    context['prediction'] = prediction
                    context['prediction_found'] = True
                    
                    #map the integer predicted_subject to its string representation
                    predicted_subject = label_mappings.get(prediction.predicted_subject, 
                                                        f"Unknown ({prediction.predicted_subject})")
                    context['predicted_subject'] = predicted_subject
                    
                    #convert comma-separated string back to list
                    recommended_subjects = prediction.recommended_subjects.split(',')
                    context['recommended_subjects'] = recommended_subjects
                    
                    #check if user has already submitted a testimonial for this prediction
                    user_testimonial = Testimonial.objects.filter(student=user, prediction=prediction).first()
                    context['has_testimonial'] = user_testimonial is not None
                    context['user_testimonial'] = user_testimonial
                else:
                    temp_prediction = request.session.pop('temp_prediction', None) #check for temporary prediction data in session
                    if temp_prediction:
                        predicted_subject = temp_prediction['predicted_subject']
                        context['predicted_subject'] = predicted_subject
                        
                        recommended_subjects = temp_prediction['recommended_subjects']
                        context['recommended_subjects'] = recommended_subjects
                        context['student_input'] = temp_prediction['student_input']
                        context['is_temporary'] = True
                    else:
                        context['error_message'] = "No predictions found for your account. Try making a prediction first."
            
        except StudentProfile.DoesNotExist:
            del request.session['student_id'] #invalid session, redirect to landing
            return redirect('landing')
    else:
        temp_prediction = request.session.pop('temp_prediction', None) #if not logged in, check for temporary prediction data
        if temp_prediction:
            predicted_subject = temp_prediction['predicted_subject']
            context['predicted_subject'] = predicted_subject
        
            recommended_subjects = temp_prediction['recommended_subjects']
            context['recommended_subjects'] = recommended_subjects
            context['student_input'] = temp_prediction['student_input']
            context['is_temporary'] = True
        else:
            return redirect('landing') #if no prediction data and not logged in
    
    return render(request, 'results.html', context)

@login_required(login_url='student_login')
def iq_test_view(request):
    if request.method == 'GET':
        #get all questions by type
        logical_questions = list(IQQuestion.objects.filter(question_type='logical'))
        verbal_questions = list(IQQuestion.objects.filter(question_type='verbal'))
        numerical_questions = list(IQQuestion.objects.filter(question_type='numerical'))
        spatial_questions = list(IQQuestion.objects.filter(question_type='spatial'))
        
        #randomly select 5 questions from each category
        selected_logical = random.sample(logical_questions, min(5, len(logical_questions)))
        selected_verbal = random.sample(verbal_questions, min(5, len(verbal_questions)))
        selected_numerical = random.sample(numerical_questions, min(5, len(numerical_questions)))
        selected_spatial = random.sample(spatial_questions, min(5, len(spatial_questions)))
        
        #combine all selected questions
        questions = selected_logical + selected_verbal + selected_numerical + selected_spatial
        
        #shuffle the questions for the test
        random.shuffle(questions)
        
        #store the selected question IDs in the session
        request.session['iq_test_questions'] = [q.id for q in questions]
        
        context = {
            'questions': questions,
        }
        return render(request, 'iq_test.html', context)
    
    elif request.method == 'POST':
        question_ids = request.session.get('iq_test_questions', [])
        if not question_ids:
            return redirect('predict')
            
        #calculate scores by category
        logical_score = 0
        verbal_score = 0
        numerical_score = 0
        spatial_score = 0
        
        for q_id in question_ids:
            question = IQQuestion.objects.get(id=q_id)
            user_answer = request.POST.get(f'question_{q_id}')
            
            if user_answer == question.correct_answer:
                if question.question_type == 'logical':
                    logical_score += 1
                elif question.question_type == 'verbal':
                    verbal_score += 1
                elif question.question_type == 'numerical':
                    numerical_score += 1
                elif question.question_type == 'spatial':
                    spatial_score += 1
        
        #create or update test result
        student_id = request.session.get('student_id')
        if student_id:
            student = StudentProfile.objects.get(id=student_id)
            #find the most recent prediction
            prediction = Prediction.objects.filter(student=student).order_by('-created_at').first()
            
            test_result = IQTestResult(
                student=student,
                prediction=prediction,
                logical_score=logical_score,
                verbal_score=verbal_score,
                numerical_score=numerical_score,
                spatial_score=spatial_score
            )
            test_result.calculate_normalized_score()
            test_result.save()
            
            #redirect to enhanced results page
            return redirect('enhanced_result', iq_result_id=test_result.id)
        else:
            #handle anonymous users
            request.session['temp_iq_results'] = {
                'logical_score': logical_score,
                'verbal_score': verbal_score,
                'numerical_score': numerical_score,
                'spatial_score': spatial_score,
                'total_score': 100 + ((logical_score + verbal_score + numerical_score + spatial_score - 6) * 5)
            }
            return redirect('result_view')

@login_required(login_url='student_login')
def enhanced_result_view(request, iq_result_id=None):
    context = {}    
    #existing label mappings
    label_mappings = {
        0: "Arts",
        1: "Business",
        2: "Healthcare",
        3: "Humanities",
        4: "STEM"
    }
    
    student_id = request.session.get('student_id')
    if student_id:
        try:
            user = StudentProfile.objects.get(id=student_id)
            context['user'] = user
            
            #get IQ test result
            if iq_result_id:
                try:
                    iq_result = IQTestResult.objects.get(id=iq_result_id, student=user)
                    context['iq_result'] = iq_result
                    context['cognitive_strengths'] = iq_result.get_suitable_areas()
                    
                    #get corresponding prediction
                    prediction = iq_result.prediction
                    if prediction:
                        context['prediction'] = prediction
                        context['prediction_found'] = True
                        
                        #map the integer predicted_subject to its string representation
                        predicted_subject = label_mappings.get(prediction.predicted_subject, 
                                                           f"Unknown ({prediction.predicted_subject})")
                        context['predicted_subject'] = predicted_subject
                        
                        #convert comma-separated string back to list
                        recommended_subjects = prediction.recommended_subjects.split(',')
                        context['recommended_subjects'] = recommended_subjects
                        
                    #calculate compatibility score between IQ results and prediction
                    compatibility = 0
                    cognitive_strengths = iq_result.get_suitable_areas()

                    if predicted_subject in cognitive_strengths:
                        #high compatibility when predicted subject matches cognitive strengths
                        base_compatibility = 85
                        iq_bonus = min(15, (iq_result.total_score - 100) // 3)
                        compatibility = base_compatibility + iq_bonus
                    else:
                        #check if there's a related field match
                        related_fields = {
                            "STEM": ["Healthcare", "Business"],
                            "Humanities": ["Arts", "Healthcare"],
                            "Business": ["STEM", "Humanities"],
                            "Arts": ["Humanities"],
                            "Healthcare": ["STEM", "Humanities"]
                        }
                        
                        related_match = any(related in cognitive_strengths for related in related_fields.get(predicted_subject, []))
                        
                        if related_match:
                            #medium compatibility when related to cognitive strengths
                            base_compatibility = 70
                            iq_bonus = min(10, (iq_result.total_score - 100) // 4)
                            compatibility = base_compatibility + iq_bonus
                        else:
                            #lower compatibility when no direct or related match
                            base_compatibility = 60
                            iq_bonus = min(5, (iq_result.total_score - 100) // 5)
                            compatibility = base_compatibility + iq_bonus

                    context['compatibility_score'] = min(100, max(55, compatibility))
                        
                except IQTestResult.DoesNotExist:
                    return redirect('result_view')  #fallback to regular results
            else:
                #fallback to standard result view logic
                return redirect('result_view')
                
        except StudentProfile.DoesNotExist:
            del request.session['student_id']
            return redirect('landing')
    else:
        #handle temp results similarly to your existing code
        temp_iq_results = request.session.get('temp_iq_results')
        temp_prediction = request.session.get('temp_prediction')
        
        if temp_iq_results and temp_prediction:
            context['predicted_subject'] = temp_prediction['predicted_subject']
            context['recommended_subjects'] = temp_prediction['recommended_subjects']
            context['student_input'] = temp_prediction['student_input']
            context['is_temporary'] = True
            context['temp_iq_results'] = temp_iq_results
        else:
            return redirect('landing')
    
    return render(request, 'enhanced_result.html', context)


@login_required(login_url='student_login')
def add_testimonial_view(request):
    if request.method == 'POST':
        student_id = request.session.get('student_id')
        if not student_id:
            return redirect('landing')

        try:
            user = StudentProfile.objects.get(id=student_id)
        except StudentProfile.DoesNotExist:
            del request.session['student_id']
            return redirect('landing')

        content = request.POST.get('content')
        rating = request.POST.get('rating')
        prediction_id = request.POST.get('prediction_id')

        try:
            prediction = Prediction.objects.get(id=prediction_id, student=user)

            if Testimonial.objects.filter(student=user, prediction=prediction).exists():
                messages.error(request, "You have already submitted feedback for this prediction.")
                return redirect('home')

            Testimonial.objects.create(student=user, prediction=prediction, name=user.full_name, content=content, rating=rating)


        except Prediction.DoesNotExist:
            messages.error(request, "The specified prediction was not found.")

    return redirect('home')

#about page function
def about(request):
    return render(request, 'about.html')

#contact page to handle form submission
@login_required(login_url='student_login')
def contact_view(request):
    student_id = request.session.get('student_id')
    context = {}
    
    if student_id:
        try:
            student = StudentProfile.objects.get(id=student_id)
            context['user'] = student
        except StudentProfile.DoesNotExist:
            if 'student_id' in request.session: #clear invalid session
                del request.session['student_id']
    
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        message = request.POST.get('message')

        ContactMessage.objects.create(name=name, email=email, message=message)
        
        messages.success(request, "Your message has been sent successfully!")
        return redirect('contact')

    return render(request, 'contact.html', context)

def student_guide_view(request):
    return render(request, 'guide.html')

#reports page function
# @login_required(login_url='student_login')
# def visuals(request):
#     img_folder = os.path.join('static', 'img') #path to the folder where images are stored
#     images = [
#         'class_distribution.png',
#         'pairplot_scores.png',
#         'attendance_distribution.png',
#         'violin_math_scores.png',
#         'histogram_scores.png',
#         'correlation_heatmap.png'
#     ]
#     image_paths = [os.path.join(img_folder, img) for img in images] #construct full paths for each image
    
#     return render(request, 'visuals.html', {'image_paths': image_paths})


@login_required(login_url='teacher_login')
def teacher_dashboard(request):
    #ensure the teacher is logged in via session
    if "teacher_email" not in request.session:
        return redirect("teacher_signin")

    teacher_email = request.session["teacher_email"]
    teacher = get_object_or_404(TeacherProfile, email=teacher_email)

    #filter students by the teacher's school
    school_students = StudentProfile.objects.filter(school=teacher.school)
    
    #get student IDs from the filtered students
    student_ids = school_students.values_list('id', flat=True)
    predictions = []
    
    for student_id in student_ids:
        #get the latest prediction for this student
        latest_prediction = Prediction.objects.filter(
            student_id=student_id
        ).order_by('-id').first()
        
        if latest_prediction:
            predictions.append(latest_prediction)

    #fetch override history for ALL students in this school
    #instead of filtering by teacher, we filter by students who belong to this school
    override_history = RecommendationOverride.objects.filter(
        student__in=school_students
    ).order_by('-timestamp')
    
    #count total students from this school
    total_students = school_students.count()

    #count accepted recommendations - make sure this logic matches your use case
    #assuming a recommendation is "accepted" when it hasn't been overridden
    overridden_students = RecommendationOverride.objects.filter(
        student__in=school_students
    ).values_list('student__id', flat=True).distinct()
    
    non_overridden = total_students - len(overridden_students)
    
    #calculate acceptance percentage
    accepted_percentage = (non_overridden / total_students) * 100 if total_students > 0 else 0

    #most popular stream
    stream_mapping = {
        0: "Arts",
        1: "Business",
        2: "Healthcare",
        3: "Humanities",
        4: "STEM",
    }
    
    popular_subject_counts = {}
    for prediction in predictions:
        subject = prediction.predicted_subject
        if subject in popular_subject_counts:
            popular_subject_counts[subject] += 1
        else:
            popular_subject_counts[subject] = 1
    
    popular_subject = None
    max_count = 0
    for subject, count in popular_subject_counts.items():
        if count > max_count:
            max_count = count
            popular_subject = subject
    
    popular_stream = stream_mapping.get(popular_subject, "N/A") if popular_subject is not None else "N/A"

    students_with_predictions = StudentProfile.objects.filter(
        id__in=Prediction.objects.values_list('student', flat=True),
        school=teacher.school
    ).distinct() #fetchs only students who have predictions and in same school with teacher for the feedback dropdown
    
    all_students_count = total_students
    students_with_predictions_count = students_with_predictions.count() 

    context = {
        "teacher": teacher,
        "predictions": predictions,
        "override_history": override_history,
        "total_students": total_students,
        "accepted_percentage": round(accepted_percentage, 1),
        "popular_stream": popular_stream,
        "students": students_with_predictions,
        "all_students_count": all_students_count,
        "students_with_predictions_count": students_with_predictions_count,
    }
    return render(request, "teacher_dashboard.html", context)

#override recomendations function 
@login_required(login_url='teacher_login')
def override_recommendation(request, prediction_id):
    if "teacher_email" not in request.session:
        return redirect("teacher_signin")

    teacher_email = request.session["teacher_email"]
    teacher = get_object_or_404(TeacherProfile, email=teacher_email)
    
    prediction = get_object_or_404(Prediction, id=prediction_id)
    
    #check if the student belongs to the teacher's school
    if prediction.student.school != teacher.school:
        messages.error(request, "You can only override recommendations for students in your school.")
        return redirect("teacher_dashboard")

    if request.method == "POST":
        new_recommendation = request.POST.get("new_recommendation")
        reason = request.POST.get("reason", "")

        try:
            new_recommendation = int(new_recommendation)
            if new_recommendation not in [0, 1, 2, 3, 4]:
                raise ValueError
        except ValueError:
            messages.error(request, "Invalid recommendation choice.")
            return redirect("teacher_dashboard")

        #save override history
        RecommendationOverride.objects.create(
            teacher=teacher,
            student=prediction.student,
            old_recommendation=prediction.predicted_subject,
            new_recommendation=new_recommendation,
            reason=reason
        )

        #update main recommendation
        prediction.predicted_subject = new_recommendation
        prediction.save()

        messages.success(request, "Recommendation successfully overridden.")

    return redirect("teacher_dashboard")


@login_required(login_url='teacher_login')
def submit_feedback(request):
    #ensure only logged-in teachers can submit feedback
    if "teacher_email" not in request.session:
        return redirect("teacher_signin")

    if request.method == "POST":
        student_id = request.POST.get("student_id")
        feedback_text = request.POST.get("feedback")

        #ensure student exists
        student = get_object_or_404(StudentProfile, id=student_id)
        
        #get teacher details
        teacher_email = request.session["teacher_email"]
        teacher = get_object_or_404(TeacherProfile, email=teacher_email)
        
        #check if the student belongs to the teacher's school
        if student.school != teacher.school:
            return HttpResponseRedirect(reverse('teacher_dashboard') + '?feedback_error=true&message=You%20can%20only%20provide%20feedback%20for%20students%20in%20your%20school')

        #check if the student has any predictions
        prediction = Prediction.objects.filter(student=student).order_by('-id').first()
        
        #if no prediction exists, redirect with an error message
        if not prediction:
            return HttpResponseRedirect(reverse('teacher_dashboard') + '?feedback_error=true&message=This%20student%20has%20not%20made%20any%20predictions%20yet')

        #create and save feedback using the Feedback model
        Feedback.objects.create(
            teacher=teacher,
            student=student,
            feedback=feedback_text
        )
        
        return HttpResponseRedirect(reverse('teacher_dashboard') + '?feedback_success=true')


@login_required(login_url='student_login')
def student_feedback(request):
    #check if user is logged in
    student_id = request.session.get('student_id')
    if not student_id:
        #if not logged in, redirect to signin page
        return redirect('student_signin')
    try:
        student = StudentProfile.objects.get(id=student_id)
    except StudentProfile.DoesNotExist:
        request.session.flush()
        return redirect('landing')
    
    #get teacher feedback for this student
    feedback_entries = Feedback.objects.filter(student=student).order_by('-timestamp')
    
    #get overrides for this student
    override_entries = RecommendationOverride.objects.filter(student=student).order_by('-timestamp')
    
    context = {
        'user': student,
        'feedback_entries': feedback_entries,
        'override_entries': override_entries
    }
    
    return render(request, 'student_feedback.html', context)

@login_required(login_url='student_login')
def logout_view(request):
    logout(request)
    return redirect('landing')